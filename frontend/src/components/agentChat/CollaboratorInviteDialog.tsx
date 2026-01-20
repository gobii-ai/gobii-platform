import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Mail, UserPlus } from 'lucide-react'

import { getCsrfToken } from '../../api/http'
import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'

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
  const [isMobile, setIsMobile] = useState(false)

  const displayName = useMemo(() => (agentName || '').trim() || 'this agent', [agentName])
  const canInvite = Boolean(inviteUrl && canManage)
  const subtitle = 'Collaborators can chat and access shared files, but cannot change settings or billing.'

  useEffect(() => {
    if (!open) {
      return
    }
    setEmail('')
    setError(null)
    setSuccess(null)
  }, [open])

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

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

  if (isMobile) {
    return (
      <AgentChatMobileSheet
        open={open}
        onClose={onClose}
        title={`Invite someone to ${displayName}`}
        subtitle={subtitle}
        icon={UserPlus}
        ariaLabel={`Invite someone to ${displayName}`}
      >
        <div className="space-y-4">{body}</div>
      </AgentChatMobileSheet>
    )
  }

  return (
    <Modal
      title={`Invite someone to ${displayName}`}
      subtitle={subtitle}
      onClose={onClose}
      icon={UserPlus}
      iconBgClass="bg-emerald-100"
      iconColorClass="text-emerald-600"
      widthClass="sm:max-w-lg"
      bodyClassName="space-y-4"
    >
      {body}
    </Modal>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, LoaderCircle, XCircle } from 'lucide-react'

import {
  acceptAgentCollaboratorInvite, declineAgentCollaboratorInvite, type AgentCollaboratorInviteResponseAction, type AgentCollaboratorInviteResponsePayload,
} from '../../api/agentCollaboratorInvites'

type InviteStatus = 'loading' | 'accepted' | 'declined' | 'issue' | 'error'

type AgentCollaboratorInviteResponsePageProps = {
  token: string
  action: AgentCollaboratorInviteResponseAction
  onNavigate: (path: string) => void
}

function issueTitle(issue: AgentCollaboratorInviteResponsePayload['issue']): string {
  if (issue === 'expired') {
    return 'Invite expired'
  }
  if (issue === 'wrong_account') {
    return 'Wrong account'
  }
  if (issue === 'already_responded') {
    return 'Invite already used'
  }
  return 'Invite unavailable'
}

function issueMessage(payload: AgentCollaboratorInviteResponsePayload | null): string {
  if (payload?.message) {
    return payload.message
  }
  if (payload?.issue === 'wrong_account') {
    return payload.invitedEmail
      ? `This invite was sent to ${payload.invitedEmail}. Switch accounts to respond to it.`
      : 'This invite is not associated with the current account.'
  }
  if (payload?.issue === 'expired') {
    return payload.invitedBy
      ? `Ask ${payload.invitedBy} to send a new invite.`
      : 'Ask the agent owner to send a new invite.'
  }
  if (payload?.issue === 'already_responded') {
    return payload.status
      ? `This invite has already been marked ${payload.status.toLowerCase()}.`
      : 'This invite has already been responded to.'
  }
  return 'This invite link is invalid or no longer available.'
}

export function AgentCollaboratorInviteResponsePage({
  token,
  action,
  onNavigate,
}: AgentCollaboratorInviteResponsePageProps) {
  const [status, setStatus] = useState<InviteStatus>('loading')
  const [payload, setPayload] = useState<AgentCollaboratorInviteResponsePayload | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let redirectTimer: number | undefined

    const respondToInvite = async () => {
      setStatus('loading')
      setPayload(null)
      setErrorMessage(null)
      try {
        const result = action === 'accept'
          ? await acceptAgentCollaboratorInvite(token)
          : await declineAgentCollaboratorInvite(token)
        if (cancelled) {
          return
        }
        setPayload(result)
        if (!result.ok) {
          setStatus('issue')
          return
        }
        setStatus(action === 'accept' ? 'accepted' : 'declined')
        if (result.redirectUrl) {
          redirectTimer = window.setTimeout(() => onNavigate(result.redirectUrl as string), 650)
        }
      } catch (error) {
        if (cancelled) {
          return
        }
        setStatus('error')
        setErrorMessage(error instanceof Error ? error.message : `Unable to ${action} this invite.`)
      }
    }

    void respondToInvite()
    return () => {
      cancelled = true
      if (redirectTimer) {
        window.clearTimeout(redirectTimer)
      }
    }
  }, [action, onNavigate, token])

  const icon = useMemo(() => {
    if (status === 'loading') {
      return <LoaderCircle className="immersive-invite-card__icon-svg immersive-invite-card__icon-svg--spin" />
    }
    if (status === 'accepted') {
      return <CheckCircle2 className="immersive-invite-card__icon-svg" />
    }
    if (status === 'declined') {
      return <XCircle className="immersive-invite-card__icon-svg" />
    }
    return <AlertTriangle className="immersive-invite-card__icon-svg" />
  }, [status])

  const title = status === 'accepted'
    ? `Joined ${payload?.agent?.name ?? 'agent'}`
    : status === 'declined'
      ? 'Invite declined'
      : status === 'loading'
        ? action === 'accept' ? 'Accepting invite' : 'Declining invite'
        : status === 'issue'
          ? issueTitle(payload?.issue)
          : `Could not ${action} invite`

  const message = status === 'accepted'
    ? 'Opening the shared agent.'
    : status === 'declined'
      ? 'Returning to your agents.'
      : status === 'loading'
        ? action === 'accept'
          ? 'Checking the invite and preparing the shared agent.'
          : 'Checking the invite and recording your response.'
        : status === 'issue'
          ? issueMessage(payload)
          : (errorMessage ?? `Unable to ${action} this invite.`)

  const actionPath = (status === 'accepted' || status === 'declined') && payload?.redirectUrl
    ? payload.redirectUrl
    : status === 'issue' && payload?.issue === 'wrong_account'
      ? '/app/profile'
      : '/app/agents'

  return (
    <section className="immersive-invite-page">
      <div className={`immersive-invite-card immersive-invite-card--${status}`}>
        <div className="immersive-invite-card__icon">{icon}</div>
        <p className="immersive-invite-card__eyebrow">Agent Invite</p>
        <h1 className="immersive-invite-card__title">{title}</h1>
        <p className="immersive-invite-card__message">{message}</p>
        {status !== 'loading' ? (
          <button
            type="button"
            className="immersive-invite-card__button"
            onClick={() => onNavigate(actionPath)}
          >
            <span>{status === 'accepted' ? 'Open agent' : 'Continue'}</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </section>
  )
}

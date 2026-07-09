import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, LoaderCircle } from 'lucide-react'

import { acceptOrganizationInvite, type OrganizationInviteAcceptPayload } from '../../api/organization'

type InviteStatus = 'loading' | 'accepted' | 'issue' | 'error'

type OrganizationInviteAcceptPageProps = {
  token: string
  onNavigate: (path: string) => void
}

function issueTitle(issue: OrganizationInviteAcceptPayload['issue']): string {
  if (issue === 'expired') {
    return 'Invite expired'
  }
  if (issue === 'wrong_account') {
    return 'Wrong account'
  }
  return 'Invite unavailable'
}

function issueMessage(payload: OrganizationInviteAcceptPayload | null): string {
  if (payload?.issue === 'wrong_account') {
    return payload.invitedEmail
      ? `This invite was sent to ${payload.invitedEmail}. Switch accounts to accept it.`
      : 'This invite is not associated with the current account.'
  }
  if (payload?.issue === 'expired') {
    return payload.invitedBy
      ? `Ask ${payload.invitedBy} to send a new invite.`
      : 'Ask the organization owner to send a new invite.'
  }
  return 'This invite link is invalid or no longer available.'
}

export function OrganizationInviteAcceptPage({ token, onNavigate }: OrganizationInviteAcceptPageProps) {
  const [status, setStatus] = useState<InviteStatus>('loading')
  const [payload, setPayload] = useState<OrganizationInviteAcceptPayload | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let redirectTimer: number | undefined

    const acceptInvite = async () => {
      setStatus('loading')
      setPayload(null)
      setErrorMessage(null)
      try {
        const result = await acceptOrganizationInvite(token)
        if (cancelled) {
          return
        }
        setPayload(result)
        if (!result.ok) {
          setStatus('issue')
          return
        }
        setStatus('accepted')
        if (result.organization) {
          window.dispatchEvent(new CustomEvent('gobii:console-context-updated', {
            detail: {
              type: 'organization',
              id: result.organization.id,
              name: result.organization.name,
            },
          }))
        }
        if (result.redirectUrl) {
          redirectTimer = window.setTimeout(() => onNavigate(result.redirectUrl as string), 650)
        }
      } catch (error) {
        if (cancelled) {
          return
        }
        setStatus('error')
        setErrorMessage(error instanceof Error ? error.message : 'Unable to accept this invite.')
      }
    }

    void acceptInvite()
    return () => {
      cancelled = true
      if (redirectTimer) {
        window.clearTimeout(redirectTimer)
      }
    }
  }, [onNavigate, token])

  const icon = useMemo(() => {
    if (status === 'loading') {
      return <LoaderCircle className="immersive-invite-card__icon-svg immersive-invite-card__icon-svg--spin" />
    }
    if (status === 'accepted') {
      return <CheckCircle2 className="immersive-invite-card__icon-svg" />
    }
    return <AlertTriangle className="immersive-invite-card__icon-svg" />
  }, [status])

  const title = status === 'accepted'
    ? `Joined ${payload?.organization?.name ?? 'team'}`
    : status === 'loading'
      ? 'Accepting invite'
      : status === 'issue'
        ? issueTitle(payload?.issue)
        : 'Could not accept invite'

  const message = status === 'accepted'
    ? 'Opening your team workspace.'
    : status === 'loading'
      ? 'Checking the invite and preparing your team workspace.'
      : status === 'issue'
        ? issueMessage(payload)
        : (errorMessage ?? 'Unable to accept this invite.')

  const actionPath = status === 'accepted' && payload?.redirectUrl
    ? payload.redirectUrl
    : status === 'issue' && payload?.issue === 'wrong_account'
      ? '/app/profile'
      : '/app/agents'

  return (
    <section className="immersive-invite-page">
      <div className={`immersive-invite-card immersive-invite-card--${status}`}>
        <div className="immersive-invite-card__icon">{icon}</div>
        <p className="immersive-invite-card__eyebrow">Team Invite</p>
        <h1 className="immersive-invite-card__title">{title}</h1>
        <p className="immersive-invite-card__message">{message}</p>
        {status !== 'loading' ? (
          <button
            type="button"
            className="immersive-invite-card__button"
            onClick={() => onNavigate(actionPath)}
          >
            <span>{status === 'accepted' ? 'Open organization' : 'Continue'}</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </section>
  )
}

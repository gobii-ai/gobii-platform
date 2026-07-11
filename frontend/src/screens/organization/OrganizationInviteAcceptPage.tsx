import { acceptOrganizationInvite, type OrganizationInviteAcceptPayload } from '../../api/organization'
import { InviteResponsePage, type InviteResponseConfig } from '../invitations/InviteResponsePage'

function issueTitle(issue: OrganizationInviteAcceptPayload['issue']): string {
  if (issue === 'expired') return 'Invite expired'
  if (issue === 'wrong_account') return 'Wrong account'
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

const ORGANIZATION_INVITE_CONFIG: InviteResponseConfig<OrganizationInviteAcceptPayload> = {
  eyebrow: 'Team Invite',
  successStatus: 'accepted',
  request: acceptOrganizationInvite,
  errorMessage: 'Unable to accept this invite.',
  title: ({ status, payload }) => status === 'accepted'
    ? `Joined ${payload?.organization?.name ?? 'team'}`
    : status === 'loading'
      ? 'Accepting invite'
      : status === 'issue' ? issueTitle(payload?.issue) : 'Could not accept invite',
  message: ({ status, payload, errorMessage }) => status === 'accepted'
    ? 'Opening your team workspace.'
    : status === 'loading'
      ? 'Checking the invite and preparing your team workspace.'
      : status === 'issue' ? issueMessage(payload) : (errorMessage ?? 'Unable to accept this invite.'),
  actionPath: ({ status, payload }) => status === 'accepted' && payload?.redirectUrl
    ? payload.redirectUrl
    : status === 'issue' && payload?.issue === 'wrong_account' ? '/app/profile' : '/app/agents',
  actionLabel: ({ status }) => status === 'accepted' ? 'Open organization' : 'Continue',
  onSuccess: (payload) => {
    if (!payload.organization) return
    window.dispatchEvent(new CustomEvent('gobii:console-context-updated', {
      detail: {
        type: 'organization',
        id: payload.organization.id,
        name: payload.organization.name,
      },
    }))
  },
}

export function OrganizationInviteAcceptPage({
  token,
  onNavigate,
}: {
  token: string
  onNavigate: (path: string) => void
}) {
  return <InviteResponsePage token={token} onNavigate={onNavigate} config={ORGANIZATION_INVITE_CONFIG} />
}

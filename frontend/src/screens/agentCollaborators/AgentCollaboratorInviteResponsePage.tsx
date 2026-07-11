import {
  acceptAgentCollaboratorInvite,
  declineAgentCollaboratorInvite,
  type AgentCollaboratorInviteResponseAction,
  type AgentCollaboratorInviteResponsePayload,
} from '../../api/agentCollaboratorInvites'
import { InviteResponsePage, type InviteResponseConfig } from '../invitations/InviteResponsePage'

function issueTitle(issue: AgentCollaboratorInviteResponsePayload['issue']): string {
  if (issue === 'expired') return 'Invite expired'
  if (issue === 'wrong_account') return 'Wrong account'
  if (issue === 'already_responded') return 'Invite already used'
  return 'Invite unavailable'
}

function issueMessage(payload: AgentCollaboratorInviteResponsePayload | null): string {
  if (payload?.message) return payload.message
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

function buildConfig(action: AgentCollaboratorInviteResponseAction): InviteResponseConfig<AgentCollaboratorInviteResponsePayload> {
  const accepted = action === 'accept'
  return {
    eyebrow: 'Agent Invite',
    successStatus: accepted ? 'accepted' : 'declined',
    request: accepted ? acceptAgentCollaboratorInvite : declineAgentCollaboratorInvite,
    errorMessage: `Unable to ${action} this invite.`,
    title: ({ status, payload }) => status === 'accepted'
      ? `Joined ${payload?.agent?.name ?? 'agent'}`
      : status === 'declined'
        ? 'Invite declined'
        : status === 'loading'
          ? accepted ? 'Accepting invite' : 'Declining invite'
          : status === 'issue' ? issueTitle(payload?.issue) : `Could not ${action} invite`,
    message: ({ status, payload, errorMessage }) => status === 'accepted'
      ? 'Opening the shared agent.'
      : status === 'declined'
        ? 'Returning to your agents.'
        : status === 'loading'
          ? accepted
            ? 'Checking the invite and preparing the shared agent.'
            : 'Checking the invite and recording your response.'
          : status === 'issue' ? issueMessage(payload) : (errorMessage ?? `Unable to ${action} this invite.`),
    actionPath: ({ status, payload }) => (status === 'accepted' || status === 'declined') && payload?.redirectUrl
      ? payload.redirectUrl
      : status === 'issue' && payload?.issue === 'wrong_account' ? '/app/profile' : '/app/agents',
    actionLabel: ({ status }) => status === 'accepted' ? 'Open agent' : 'Continue',
  }
}

const INVITE_CONFIGS = {
  accept: buildConfig('accept'),
  decline: buildConfig('decline'),
}

export function AgentCollaboratorInviteResponsePage({
  token,
  action,
  onNavigate,
}: {
  token: string
  action: AgentCollaboratorInviteResponseAction
  onNavigate: (path: string) => void
}) {
  return <InviteResponsePage token={token} onNavigate={onNavigate} config={INVITE_CONFIGS[action]} />
}

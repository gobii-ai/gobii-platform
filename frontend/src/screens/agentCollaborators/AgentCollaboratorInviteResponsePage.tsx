import {
  acceptAgentCollaboratorInvite,
  agentCollaboratorInviteIssueMessage,
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
  return payload ? agentCollaboratorInviteIssueMessage(payload) : 'This invite is invalid or no longer available.'
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

import { jsonRequest } from './http'

export type AgentCollaboratorInviteIssue = 'invalid' | 'expired' | 'wrong_account' | 'already_responded'

export type AgentCollaboratorInviteResponseAction = 'accept' | 'decline'

export type AgentCollaboratorInviteResponsePayload = {
  ok: boolean
  issue?: AgentCollaboratorInviteIssue
  action?: AgentCollaboratorInviteResponseAction
  message?: string
  status?: string
  invitedEmail?: string
  invitedBy?: string
  agent?: {
    id: string
    name: string
  }
  redirectUrl?: string
}

export function agentCollaboratorInviteIssueMessage(payload: AgentCollaboratorInviteResponsePayload): string {
  if (payload.message) return payload.message
  if (payload.issue === 'wrong_account') {
    return payload.invitedEmail
      ? `This invite was sent to ${payload.invitedEmail}. Switch accounts to respond to it.`
      : 'This invite is not associated with the current account.'
  }
  if (payload.issue === 'expired') {
    return payload.invitedBy
      ? `Ask ${payload.invitedBy} to send a new invite.`
      : 'Ask the agent owner to send a new invite.'
  }
  if (payload.issue === 'already_responded') {
    return payload.status
      ? `This invite has already been marked ${payload.status.toLowerCase()}.`
      : 'This invite has already been responded to.'
  }
  return 'This invite is invalid or no longer available.'
}

export async function respondToAgentCollaboratorInvite(
  url: string,
): Promise<AgentCollaboratorInviteResponsePayload> {
  const payload = await jsonRequest<AgentCollaboratorInviteResponsePayload>(url, {
    method: 'POST',
    includeCsrf: true,
  })
  if (!payload.ok) {
    throw new Error(agentCollaboratorInviteIssueMessage(payload))
  }
  return payload
}

export function acceptAgentCollaboratorInvite(token: string): Promise<AgentCollaboratorInviteResponsePayload> {
  return jsonRequest<AgentCollaboratorInviteResponsePayload>(`/console/api/agent-collaborator-invites/${token}/accept/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

export function declineAgentCollaboratorInvite(token: string): Promise<AgentCollaboratorInviteResponsePayload> {
  return jsonRequest<AgentCollaboratorInviteResponsePayload>(`/console/api/agent-collaborator-invites/${token}/decline/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

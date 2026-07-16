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

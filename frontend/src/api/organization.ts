import { jsonFetch, jsonRequest } from './http'

export type OrganizationRole = {
  value: string
  label: string
}

export type OrganizationMember = {
  userId: string
  name: string
  email: string
  role: string
  roleLabel: string
  isCurrentUser: boolean
  canUpdateRole: boolean
  canRemove: boolean
}

export type OrganizationInvite = {
  token: string
  email: string
  role: string
  roleLabel: string
  invitedBy: string
  sentAt: string | null
  expiresAt: string | null
}

export type CurrentOrganizationPayload = {
  organization: {
    id: string
    name: string
    slug: string
    plan: string
  }
  viewer: {
    role: string
    roleLabel: string
    canEditOrganization: boolean
    canManageMembers: boolean
    canManageBilling: boolean
  }
  roles: OrganizationRole[]
  members: OrganizationMember[]
  pendingInvites: OrganizationInvite[]
  billing: {
    purchasedSeats: number | null
    seatsReserved: number | null
    seatsAvailable: number | null
  } | null
}

export type OrganizationInviteAcceptIssue = 'invalid' | 'expired' | 'wrong_account'

export type OrganizationInviteAcceptPayload = {
  ok: boolean
  issue?: OrganizationInviteAcceptIssue
  action?: 'accept'
  invitedEmail?: string
  invitedBy?: string
  organization?: {
    id: string
    name: string
  }
  redirectUrl?: string
}

const CURRENT_ORGANIZATION_URL = '/console/api/organization/'

export function fetchCurrentOrganization(signal?: AbortSignal): Promise<CurrentOrganizationPayload> {
  return jsonFetch<CurrentOrganizationPayload>(CURRENT_ORGANIZATION_URL, { signal })
}

export function updateCurrentOrganizationName(name: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(CURRENT_ORGANIZATION_URL, {
    method: 'PATCH',
    json: { name },
    includeCsrf: true,
  })
}

export function inviteOrganizationMember(email: string, role: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>('/console/api/organization/invites/', {
    method: 'POST',
    json: { email, role },
    includeCsrf: true,
  })
}

export function revokeOrganizationInvite(token: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(`/console/api/organization/invites/${token}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function acceptOrganizationInvite(token: string): Promise<OrganizationInviteAcceptPayload> {
  return jsonRequest<OrganizationInviteAcceptPayload>(`/console/api/organizations/invites/${token}/accept/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

export function updateOrganizationMemberRole(userId: string, role: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(`/console/api/organization/members/${userId}/`, {
    method: 'PATCH',
    json: { role },
    includeCsrf: true,
  })
}

export function removeOrganizationMember(userId: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(`/console/api/organization/members/${userId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

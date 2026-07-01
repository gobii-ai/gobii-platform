import { jsonFetch, jsonRequest } from './http'
import type { IntelligenceTierKey, LlmIntelligenceConfig } from '../types/llmIntelligence'

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

export type OrganizationTemplate = {
  id: string
  name: string
  tagline: string
  category: string
  preferredLlmTier: IntelligenceTierKey
  sourceAgentName: string | null
  createdBy: string | null
  scheduleDescription: string | null
}

export type OrganizationTemplateEditorPayload = {
  name: string
  tagline: string
  charter: string
  preferredLlmTier: IntelligenceTierKey
}

export type OrganizationTemplateDetailPayload = {
  template: OrganizationTemplateEditorPayload & {
    id: string
  }
}

export type OrganizationTemplateSourceAgent = {
  id: string
  name: string
}

export type CurrentOrganizationTemplatesPayload = {
  organization: {
    id: string
    name: string
  }
  viewer: {
    canManageTemplates: boolean
  }
  templates: OrganizationTemplate[]
  sourceAgents: OrganizationTemplateSourceAgent[]
  llmIntelligence: LlmIntelligenceConfig | null
  created?: boolean
  templateId?: string
  template?: OrganizationTemplateDetailPayload['template']
}

export type OrganizationTemplateLaunchPayload = {
  templateId: string
  redirectUrl: string
}

export type CurrentOrganizationPayload = {
  organization: {
    id: string
    name: string
    slug: string
    plan: string
    customInstructions: string
    customInstructionsMaxChars: number
    membersCanCreateAgents: boolean
  }
  viewer: {
    role: string
    roleLabel: string
    canEditOrganization: boolean
    canEditCustomInstructions: boolean
    canEditMemberAgentCreation: boolean
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
const CURRENT_ORGANIZATION_TEMPLATES_URL = '/console/api/organization/templates/'

export function currentOrganizationTemplatesQueryKey(organizationId?: string | null) {
  return ['current-organization-templates', organizationId ?? 'current'] as const
}

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

export function updateCurrentOrganizationCustomInstructions(customInstructions: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(CURRENT_ORGANIZATION_URL, {
    method: 'PATCH',
    json: { customInstructions },
    includeCsrf: true,
  })
}

export function updateCurrentOrganizationMemberAgentCreation(membersCanCreateAgents: boolean): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(CURRENT_ORGANIZATION_URL, {
    method: 'PATCH',
    json: { membersCanCreateAgents },
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

export function resendOrganizationInvite(token: string): Promise<CurrentOrganizationPayload> {
  return jsonRequest<CurrentOrganizationPayload>(`/console/api/organization/invites/${token}/resend/`, {
    method: 'POST',
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

export function fetchCurrentOrganizationTemplates(signal?: AbortSignal): Promise<CurrentOrganizationTemplatesPayload> {
  return jsonFetch<CurrentOrganizationTemplatesPayload>(CURRENT_ORGANIZATION_TEMPLATES_URL, { signal })
}

export function createOrganizationTemplate(sourceAgentId: string): Promise<CurrentOrganizationTemplatesPayload> {
  return jsonRequest<CurrentOrganizationTemplatesPayload>(CURRENT_ORGANIZATION_TEMPLATES_URL, {
    method: 'POST',
    json: { sourceAgentId },
    includeCsrf: true,
  })
}

export function createOrganizationTemplateFromScratch(
  payload: OrganizationTemplateEditorPayload,
): Promise<CurrentOrganizationTemplatesPayload> {
  return jsonRequest<CurrentOrganizationTemplatesPayload>(CURRENT_ORGANIZATION_TEMPLATES_URL, {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}

export function fetchOrganizationTemplateDetail(
  templateId: string,
  signal?: AbortSignal,
): Promise<OrganizationTemplateDetailPayload> {
  return jsonFetch<OrganizationTemplateDetailPayload>(`/console/api/organization/templates/${templateId}/`, { signal })
}

export function updateOrganizationTemplate(
  templateId: string,
  payload: OrganizationTemplateEditorPayload,
): Promise<CurrentOrganizationTemplatesPayload> {
  return jsonRequest<CurrentOrganizationTemplatesPayload>(`/console/api/organization/templates/${templateId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}

export function deactivateOrganizationTemplate(templateId: string): Promise<CurrentOrganizationTemplatesPayload> {
  return jsonRequest<CurrentOrganizationTemplatesPayload>(`/console/api/organization/templates/${templateId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function launchOrganizationTemplate(templateId: string): Promise<OrganizationTemplateLaunchPayload> {
  return jsonRequest<OrganizationTemplateLaunchPayload>(`/console/api/organization/templates/${templateId}/launch/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

import { jsonFetch, jsonRequest } from './http'

export type ConsoleContextType = 'personal' | 'organization'

export type ConsoleContext = {
  type: ConsoleContextType
  id: string
  name: string
  canCreateAgents?: boolean
  personalSignupPreviewCreateAvailable?: boolean
}

export type ConsoleContextOption = ConsoleContext & {
  role?: string | null
}

type ConsoleContextPayload = {
  type: ConsoleContextType
  id: string
  name: string
  canCreateAgents?: boolean
  personalSignupPreviewCreateAvailable?: boolean
}

type ConsoleContextResponsePayload = {
  context: ConsoleContextPayload
  personal: { id: string; name: string }
  organizations: { id: string; name: string; role: string | null; canCreateAgents?: boolean }[]
  organizations_enabled: boolean
  requested_agent_status?: 'deleted' | 'missing' | null
}

type SwitchContextResponsePayload = {
  success: boolean
  context: ConsoleContextPayload
  error?: string
}

type CreateOrganizationResponsePayload = {
  organization: { id: string; name: string; role: string | null }
  context: ConsoleContextPayload
}

export type ConsoleContextData = {
  context: ConsoleContext
  personal: ConsoleContext
  organizations: ConsoleContextOption[]
  organizationsEnabled: boolean
  requestedAgentStatus?: 'deleted' | 'missing' | null
}

export async function fetchConsoleContext(options: { forAgentId?: string } = {}): Promise<ConsoleContextData> {
  const query = options.forAgentId
    ? `?for_agent=${encodeURIComponent(options.forAgentId)}`
    : ''
  const payload = await jsonFetch<ConsoleContextResponsePayload>(`/console/switch-context/${query}`)
  return {
    context: payload.context,
    personal: {
      type: 'personal',
      id: payload.personal.id,
      name: payload.personal.name,
      canCreateAgents: true,
    },
    organizations: payload.organizations.map((org) => ({
      type: 'organization',
      id: org.id,
      name: org.name,
      role: org.role ?? null,
      canCreateAgents: org.canCreateAgents,
    })),
    organizationsEnabled: payload.organizations_enabled,
    requestedAgentStatus: payload.requested_agent_status ?? null,
  }
}

export async function switchConsoleContext(
  context: ConsoleContext,
  options: { persistSession?: boolean } = {},
): Promise<ConsoleContext> {
  const persistSession = options.persistSession !== false
  const body = persistSession ? context : { ...context, persist: false }
  const payload = await jsonRequest<SwitchContextResponsePayload>('/console/switch-context/', {
    method: 'POST',
    json: body,
    includeCsrf: true,
  })
  if (!payload.success) {
    throw new Error(payload.error || 'Unable to switch context')
  }
  return payload.context
}

export async function createOrganization(name: string): Promise<{
  organization: ConsoleContextOption
  context: ConsoleContext
}> {
  const payload = await jsonRequest<CreateOrganizationResponsePayload>('/console/api/organizations/', {
    method: 'POST',
    json: { name },
    includeCsrf: true,
  })
  return {
    organization: {
      type: 'organization',
      id: payload.organization.id,
      name: payload.organization.name,
      role: payload.organization.role ?? null,
    },
    context: payload.context,
  }
}

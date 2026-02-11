import { jsonFetch, jsonRequest } from './http'

export type ConsoleContextType = 'personal' | 'organization'

export type ConsoleContext = {
  type: ConsoleContextType
  id: string
  name: string
}

export type ConsoleContextOption = ConsoleContext & {
  role?: string | null
}

type ConsoleContextPayload = {
  type: ConsoleContextType
  id: string
  name: string
}

type ConsoleContextResponsePayload = {
  context: ConsoleContextPayload
  personal: { id: string; name: string }
  organizations: { id: string; name: string; role: string | null }[]
  organizations_enabled: boolean
}

type SwitchContextResponsePayload = {
  success: boolean
  context: ConsoleContextPayload
  error?: string
}

export type ConsoleContextData = {
  context: ConsoleContext
  personal: ConsoleContext
  organizations: ConsoleContextOption[]
  organizationsEnabled: boolean
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
    },
    organizations: payload.organizations.map((org) => ({
      type: 'organization',
      id: org.id,
      name: org.name,
      role: org.role ?? null,
    })),
    organizationsEnabled: payload.organizations_enabled,
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

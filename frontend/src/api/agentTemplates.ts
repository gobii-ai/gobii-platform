import { jsonRequest } from './http'

export type TemplateShareInfoResponse = {
  agentId: string
  agentName: string
  canShare: boolean
  disabledReason?: string | null
  publicProfileHandle?: string | null
  suggestedHandle?: string | null
  templateUrl?: string | null
  templateSlug?: string | null
  displayName?: string | null
}

export type TemplateCloneResponse = TemplateShareInfoResponse & {
  created: boolean
  templateUrl: string
  templateSlug: string
  publicProfileHandle: string
}

export function cloneAgentTemplate(agentId: string, handle?: string | null): Promise<TemplateCloneResponse> {
  return jsonRequest<TemplateCloneResponse>(`/console/api/agents/${agentId}/templates/clone/`, {
    method: 'POST',
    json: handle ? { handle } : {},
    includeCsrf: true,
  })
}

export function fetchAgentTemplateShareInfo(agentId: string): Promise<TemplateShareInfoResponse> {
  return jsonRequest<TemplateShareInfoResponse>(`/console/api/agents/${agentId}/templates/clone/`)
}

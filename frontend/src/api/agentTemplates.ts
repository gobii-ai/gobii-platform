import { jsonRequest } from './http'

export type TemplateCloneResponse = {
  created: boolean
  templateUrl: string
  templateSlug: string
  publicProfileHandle: string
  displayName?: string | null
}

export function cloneAgentTemplate(agentId: string, handle?: string | null): Promise<TemplateCloneResponse> {
  return jsonRequest<TemplateCloneResponse>(`/console/api/agents/${agentId}/templates/clone/`, {
    method: 'POST',
    json: handle ? { handle } : {},
    includeCsrf: true,
  })
}

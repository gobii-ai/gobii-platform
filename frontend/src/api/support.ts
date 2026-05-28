import { jsonRequest } from './http'
import type { ConsoleContext } from './context'

export type AppSupportRequestPayload = {
  message: string
  pageUrl?: string
  agentId?: string | null
  agentName?: string | null
  workspaceContext?: Pick<ConsoleContext, 'type' | 'id' | 'name'> | null
}

export type AppSupportRequestResponse = {
  ok: boolean
  message?: string
}

export async function sendAppSupportRequest(payload: AppSupportRequestPayload): Promise<AppSupportRequestResponse> {
  return jsonRequest<AppSupportRequestResponse>('/console/api/support/request/', {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

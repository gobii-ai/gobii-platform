import { jsonRequest } from './http'

export type PrequalifyPayload = Record<string, string>

export type PrequalifyResponse = {
  ok: boolean
  message?: string
  errors?: string[]
}

export async function submitPrequalify(
  url: string,
  payload: PrequalifyPayload,
): Promise<PrequalifyResponse> {
  return jsonRequest<PrequalifyResponse>(url, {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}

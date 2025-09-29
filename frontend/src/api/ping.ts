import { jsonFetch } from './http'

export interface PingResponse {
  pong: boolean
  user?: string
}

export function fetchPing(signal?: AbortSignal): Promise<PingResponse> {
  return jsonFetch<PingResponse>('/api/v1/ping/', {
    method: 'GET',
    signal,
  })
}

import { jsonFetch } from './http'
import type { SystemStatusPayload } from '../types/systemStatus'

export function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatusPayload> {
  return jsonFetch<SystemStatusPayload>('/console/api/status/', { signal })
}

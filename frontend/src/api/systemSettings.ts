import { jsonFetch, jsonRequest } from './http'

export type SystemSetting = {
  key: string
  label: string
  description: string
  category: string
  value_type: 'int' | 'float' | 'bool'
  unit?: string | null
  min_value?: number | null
  disable_value?: number | null
  env_var: string
  env_set: boolean
  db_value: number | boolean | null
  effective_value: number | boolean
  source: 'database' | 'env' | 'default'
  fallback_value: number | boolean
  fallback_source: 'env' | 'default'
}

export type SystemSettingsResponse = {
  settings: SystemSetting[]
}

const base = '/system-settings/api'

export function fetchSystemSettings(signal?: AbortSignal): Promise<SystemSettingsResponse> {
  return jsonFetch<SystemSettingsResponse>(`${base}/`, { signal })
}

export function updateSystemSetting(key: string, payload: { value?: string | number | boolean; clear?: boolean }) {
  return jsonRequest<{ ok: boolean; setting: SystemSetting }>(`${base}/${key}/`, {
    method: 'PATCH',
    includeCsrf: true,
    json: payload,
  })
}

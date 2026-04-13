import { jsonFetch, jsonRequest } from './http'

export type SystemSkillFieldDTO = {
  key: string
  name: string
  description: string
  required: boolean
  default: string | null
}

export type SystemSkillDefinitionDTO = {
  skill_key: string
  name: string
  search_summary: string
  fields: SystemSkillFieldDTO[]
  default_values: Record<string, string>
  setup_instructions: string
}

export type SystemSkillProfileDTO = {
  id: string
  skill_key: string
  profile_key: string
  label: string
  is_default: boolean
  created_at: string | null
  updated_at: string | null
  complete: boolean
  present_keys: string[]
  missing_required_keys: string[]
}

export type SystemSkillProfileListResponse = {
  owner_scope: string
  definition: SystemSkillDefinitionDTO
  profiles: SystemSkillProfileDTO[]
}

export type SystemSkillProfileMutationResponse = {
  profile: SystemSkillProfileDTO
  message: string
  owner_scope?: string
  definition?: SystemSkillDefinitionDTO
}

export type CreateSystemSkillProfilePayload = {
  profile_key: string
  label?: string
  is_default?: boolean
  values?: Record<string, string | null>
}

export type UpdateSystemSkillProfilePayload = {
  label?: string
  is_default?: true
  values?: Record<string, string | null>
}

export function fetchSystemSkillProfiles(
  listUrl: string,
  signal?: AbortSignal,
): Promise<SystemSkillProfileListResponse> {
  return jsonFetch<SystemSkillProfileListResponse>(listUrl, { signal })
}

export function createSystemSkillProfile(
  listUrl: string,
  data: CreateSystemSkillProfilePayload,
): Promise<SystemSkillProfileMutationResponse> {
  return jsonRequest<SystemSkillProfileMutationResponse>(listUrl, {
    method: 'POST',
    json: data,
    includeCsrf: true,
  })
}

export function updateSystemSkillProfile(
  detailUrl: string,
  data: UpdateSystemSkillProfilePayload,
): Promise<SystemSkillProfileMutationResponse> {
  return jsonRequest<SystemSkillProfileMutationResponse>(detailUrl, {
    method: 'PATCH',
    json: data,
    includeCsrf: true,
  })
}

export function deleteSystemSkillProfile(detailUrl: string): Promise<{ ok: boolean; message: string }> {
  return jsonRequest(detailUrl, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function setDefaultSystemSkillProfile(
  defaultUrl: string,
): Promise<SystemSkillProfileMutationResponse> {
  return jsonRequest<SystemSkillProfileMutationResponse>(defaultUrl, {
    method: 'POST',
    json: {},
    includeCsrf: true,
  })
}

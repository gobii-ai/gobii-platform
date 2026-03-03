import { jsonRequest } from './http'
export const USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE = 'agent.chat.roster.sort_mode' as const

export type UserPreferencesMap = Record<string, unknown>

type UserPreferencesPayload = {
  preferences: UserPreferencesMap
}

type UserPreferencesResponse = {
  preferences?: UserPreferencesMap
}

function normalizePreferences(preferences: unknown): UserPreferencesMap {
  if (!preferences || typeof preferences !== 'object' || Array.isArray(preferences)) {
    return {}
  }
  return preferences as UserPreferencesMap
}

export async function updateUserPreferences(
  payload: UserPreferencesPayload,
): Promise<{ preferences: UserPreferencesMap }> {
  const response = await jsonRequest<UserPreferencesResponse>('/console/api/user/preferences/', {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })

  return {
    preferences: normalizePreferences(response.preferences),
  }
}

export async function fetchUserPreferences(): Promise<{ preferences: UserPreferencesMap }> {
  const response = await jsonRequest<UserPreferencesResponse>('/console/api/user/preferences/')
  return {
    preferences: normalizePreferences(response.preferences),
  }
}

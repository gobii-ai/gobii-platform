import { jsonRequest } from './http'
export const USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE = 'agent.chat.roster.sort_mode' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS = 'agent.chat.roster.favorite_agent_ids' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS = 'agent.chat.muted_agent_ids' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED = 'agent.chat.insights_panel.expanded' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT = 'agent.chat.insights_panel.expanded_by_agent' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_SUGGESTIONS_ENABLED = 'agent.chat.suggestions.enabled' as const
export const USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED = 'agent.chat.notifications.enabled' as const

export type UserPreferencesMap = Record<string, unknown>
export type InsightsPanelExpandedByAgent = Record<string, boolean>

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

export function parseFavoriteAgentIdsPreference(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }

  const normalized: string[] = []
  const seen = new Set<string>()
  for (const entry of value) {
    if (typeof entry !== 'string') {
      continue
    }
    const candidate = entry.trim()
    if (!candidate || seen.has(candidate)) {
      continue
    }
    seen.add(candidate)
    normalized.push(candidate)
  }

  return normalized
}

export function parseBooleanPreference(value: unknown): boolean {
  return value === true
}

export function parseNullableBooleanPreference(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

export function parseInsightsPanelExpandedByAgentPreference(value: unknown): InsightsPanelExpandedByAgent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }

  const entries: Array<[string, boolean]> = []
  for (const [agentId, expanded] of Object.entries(value)) {
    const normalizedAgentId = agentId.trim()
    if (normalizedAgentId && typeof expanded === 'boolean') {
      entries.push([normalizedAgentId, expanded])
    }
  }
  return Object.fromEntries(entries)
}

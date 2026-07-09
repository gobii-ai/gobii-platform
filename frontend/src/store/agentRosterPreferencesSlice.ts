import type { QueryClient } from '@tanstack/react-query'
import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import { parseBooleanPreference, parseFavoriteAgentIdsPreference, parseNullableBooleanPreference, updateUserPreferences, USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED, USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS, USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED, USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS, USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE } from '../api/userPreferences'
import type { AgentRosterSortMode } from '../types/agentRoster'
import { parseAgentRosterSortMode } from '../util/agentRosterSort'
import type { AppDispatch, RootState } from './appStore'

type PreferenceFieldState<T> = {
  value: T
  persistedValue: T
  hydrated: boolean
}

export type AgentRosterPreferencesState = {
  sortMode: PreferenceFieldState<AgentRosterSortMode>
  favoriteAgentIds: PreferenceFieldState<string[]>
  mutedAgentIds: PreferenceFieldState<string[]>
  insightsPanelExpanded: PreferenceFieldState<boolean | null>
  agentChatNotificationsEnabled: PreferenceFieldState<boolean>
}

export type AgentRosterPreferencesHydrationPayload = {
  sortMode?: unknown
  favoriteAgentIds?: unknown
  mutedAgentIds?: unknown
  insightsPanelExpanded?: unknown
  agentChatNotificationsEnabled?: unknown
}

type AgentRosterPreferenceField = keyof AgentRosterPreferencesState

type AgentRosterQueryData = {
  agentRosterSortMode?: AgentRosterSortMode
  favoriteAgentIds?: string[]
  mutedAgentIds?: string[]
  insightsPanelExpanded?: boolean | null
  agentChatNotificationsEnabled?: boolean
}

type AgentRosterPreferenceConfig = {
  preferenceKey: string
  rosterQueryField: keyof AgentRosterQueryData
  hydrateOnce: boolean
  normalize: (value: unknown) => unknown
}

const AGENT_ROSTER_QUERY_KEY = ['agent-roster'] as const

const preferenceConfig = {
  sortMode: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
    rosterQueryField: 'agentRosterSortMode',
    hydrateOnce: true,
    normalize: parseAgentRosterSortMode,
  },
  favoriteAgentIds: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
    rosterQueryField: 'favoriteAgentIds',
    hydrateOnce: false,
    normalize: parseFavoriteAgentIdsPreference,
  },
  mutedAgentIds: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS,
    rosterQueryField: 'mutedAgentIds',
    hydrateOnce: false,
    normalize: parseFavoriteAgentIdsPreference,
  },
  insightsPanelExpanded: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED,
    rosterQueryField: 'insightsPanelExpanded',
    hydrateOnce: true,
    normalize: parseNullableBooleanPreference,
  },
  agentChatNotificationsEnabled: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED,
    rosterQueryField: 'agentChatNotificationsEnabled',
    hydrateOnce: true,
    normalize: parseBooleanPreference,
  },
} satisfies Record<AgentRosterPreferenceField, AgentRosterPreferenceConfig>

const initialState: AgentRosterPreferencesState = {
  sortMode: createPreferenceField('recent' as AgentRosterSortMode),
  favoriteAgentIds: createPreferenceField<string[]>([]),
  mutedAgentIds: createPreferenceField<string[]>([]),
  insightsPanelExpanded: createPreferenceField<boolean | null>(null),
  agentChatNotificationsEnabled: createPreferenceField(false),
}

function createPreferenceField<T>(value: T): PreferenceFieldState<T> {
  return {
    value,
    persistedValue: value,
    hydrated: false,
  }
}

function sameStringList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function samePreferenceValue(left: unknown, right: unknown): boolean {
  if (Array.isArray(left) && Array.isArray(right)) {
    return sameStringList(left, right)
  }
  return Object.is(left, right)
}

function normalizeFieldValue<K extends AgentRosterPreferenceField>(
  field: K,
  value: unknown,
): AgentRosterPreferencesState[K]['value'] {
  return preferenceConfig[field].normalize(value) as AgentRosterPreferencesState[K]['value']
}

function updateRosterPreferenceInCache<K extends AgentRosterPreferenceField>(
  queryClient: QueryClient | null | undefined,
  field: K,
  nextValue: AgentRosterPreferencesState[K]['value'],
): void {
  if (!queryClient) {
    return
  }
  const queryField = preferenceConfig[field].rosterQueryField
  queryClient.setQueriesData<AgentRosterQueryData>(
    { queryKey: AGENT_ROSTER_QUERY_KEY },
    (current) => {
      if (!current || typeof current !== 'object') {
        return current
      }
      const currentValue = current[queryField]
      if (Array.isArray(currentValue) && Array.isArray(nextValue) && sameStringList(currentValue, nextValue)) {
        return current
      }
      if (Object.is(currentValue, nextValue)) {
        return current
      }
      return {
        ...current,
        [queryField]: nextValue,
      }
    },
  )
}

function assignPreferenceValue<K extends AgentRosterPreferenceField>(
  state: AgentRosterPreferencesState,
  field: K,
  value: AgentRosterPreferencesState[K]['value'],
  options: { persisted?: boolean } = {},
): void {
  const target = state[field] as PreferenceFieldState<AgentRosterPreferencesState[K]['value']>
  const persistedValue = options.persisted ? value : target.persistedValue
  if (
    target.hydrated
    && samePreferenceValue(target.value, value)
    && samePreferenceValue(target.persistedValue, persistedValue)
  ) {
    return
  }
  target.value = value
  target.hydrated = true
  if (options.persisted) {
    target.persistedValue = value
  }
}

const agentRosterPreferencesSlice = createSlice({
  name: 'agentRosterPreferences',
  initialState,
  reducers: {
    hydratedFromRoster(state, action: PayloadAction<AgentRosterPreferencesHydrationPayload>) {
      const payload = action.payload
      for (const field of Object.keys(preferenceConfig) as AgentRosterPreferenceField[]) {
        if (payload[field] === undefined || (preferenceConfig[field].hydrateOnce && state[field].hydrated)) {
          continue
        }
        assignPreferenceValue(state, field, normalizeFieldValue(field, payload[field]), { persisted: true })
      }
    },
    preferenceOptimisticallySet<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K; value: AgentRosterPreferencesState[K]['value'] }>,
    ) {
      assignPreferenceValue(state, action.payload.field, action.payload.value)
    },
    preferencePersisted<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K; value: AgentRosterPreferencesState[K]['value'] }>,
    ) {
      assignPreferenceValue(state, action.payload.field, action.payload.value, {
        persisted: true,
      })
    },
    preferenceRolledBack<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K }>,
    ) {
      const target = state[action.payload.field] as PreferenceFieldState<AgentRosterPreferencesState[K]['value']>
      target.value = target.persistedValue
    },
  },
})

export const agentRosterPreferencesActions = agentRosterPreferencesSlice.actions
export const agentRosterPreferencesReducer = agentRosterPreferencesSlice.reducer

export function persistAgentRosterPreference<K extends AgentRosterPreferenceField>(
  field: K,
  value: AgentRosterPreferencesState[K]['value'],
) {
  return async (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
    const normalizedValue = normalizeFieldValue(field, value)
    dispatch(agentRosterPreferencesActions.preferenceOptimisticallySet({ field, value: normalizedValue }))
    updateRosterPreferenceInCache(extra?.queryClient, field, normalizedValue)
    const previousValue = getState().agentRosterPreferences[field].persistedValue
    const preferenceKey = preferenceConfig[field].preferenceKey
    try {
      const response = await updateUserPreferences({
        preferences: {
          [preferenceKey]: normalizedValue,
        },
      })
      const persistedValue = normalizeFieldValue(field, response.preferences[preferenceKey])
      dispatch(agentRosterPreferencesActions.preferencePersisted({ field, value: persistedValue }))
      updateRosterPreferenceInCache(extra?.queryClient, field, persistedValue)
    } catch {
      dispatch(agentRosterPreferencesActions.preferenceRolledBack({
        field,
      }))
      updateRosterPreferenceInCache(extra?.queryClient, field, previousValue)
    }
  }
}

export function toggleAgentRosterStringPreference(field: 'favoriteAgentIds' | 'mutedAgentIds', agentId: string) {
  return (dispatch: AppDispatch, getState: () => RootState) => {
    const currentValue = getState().agentRosterPreferences[field].value
    const nextValue = currentValue.includes(agentId)
      ? currentValue.filter((candidate) => candidate !== agentId)
      : [...currentValue, agentId]
    return dispatch(persistAgentRosterPreference(field, nextValue))
  }
}

export const selectAgentRosterPreferencesState = (state: RootState): AgentRosterPreferencesState => state.agentRosterPreferences
export const selectAgentRosterSortMode = (state: RootState): AgentRosterSortMode => state.agentRosterPreferences.sortMode.value
export const selectFavoriteAgentIds = (state: RootState): string[] => state.agentRosterPreferences.favoriteAgentIds.value
export const selectMutedAgentIds = (state: RootState): string[] => state.agentRosterPreferences.mutedAgentIds.value
export const selectInsightsPanelExpandedPreference = (state: RootState): boolean | null => state.agentRosterPreferences.insightsPanelExpanded.value
export const selectAgentChatNotificationsEnabled = (state: RootState): boolean => state.agentRosterPreferences.agentChatNotificationsEnabled.value

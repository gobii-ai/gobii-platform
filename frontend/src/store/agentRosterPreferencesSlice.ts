import type { QueryClient } from '@tanstack/react-query'
import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import {
  parseBooleanPreference,
  parseFavoriteAgentIdsPreference,
  parseInsightsPanelExpandedByAgentPreference,
  updateUserPreferences,
  USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT,
  USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
  USER_PREFERENCE_KEY_AGENT_CHAT_SUGGESTIONS_ENABLED,
  type InsightsPanelExpandedByAgent,
} from '../api/userPreferences'
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
  insightsPanelExpandedByAgent: PreferenceFieldState<InsightsPanelExpandedByAgent>
  suggestionsEnabled: PreferenceFieldState<boolean>
  agentChatNotificationsEnabled: PreferenceFieldState<boolean>
}

export type AgentRosterPreferencesHydrationPayload = {
  sortMode?: unknown
  favoriteAgentIds?: unknown
  mutedAgentIds?: unknown
  insightsPanelExpandedByAgent?: unknown
  suggestionsEnabled?: unknown
  agentChatNotificationsEnabled?: unknown
}

type AgentRosterPreferenceField = keyof AgentRosterPreferencesState

type AgentRosterQueryData = {
  agentRosterSortMode?: AgentRosterSortMode
  favoriteAgentIds?: string[]
  mutedAgentIds?: string[]
  insightsPanelExpandedByAgent?: InsightsPanelExpandedByAgent
  suggestionsEnabled?: boolean
  agentChatNotificationsEnabled?: boolean
}

type AgentRosterPreferenceConfig = {
  preferenceKey: string
  rosterQueryField: keyof AgentRosterQueryData
  hydrateOnce: boolean
  normalize: (value: unknown) => unknown
}

const AGENT_ROSTER_QUERY_KEY = ['agent-roster'] as const
const insightsPanelWriteQueueByAgent = new Map<string, Promise<void>>()
const latestInsightsPanelWriteByAgent = new Map<string, symbol>()

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
  insightsPanelExpandedByAgent: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT,
    rosterQueryField: 'insightsPanelExpandedByAgent',
    hydrateOnce: true,
    normalize: parseInsightsPanelExpandedByAgentPreference,
  },
  suggestionsEnabled: {
    preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_SUGGESTIONS_ENABLED,
    rosterQueryField: 'suggestionsEnabled',
    hydrateOnce: true,
    normalize: parseBooleanPreference,
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
  insightsPanelExpandedByAgent: createPreferenceField<InsightsPanelExpandedByAgent>({}),
  suggestionsEnabled: createPreferenceField(true),
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
      if (samePreferenceValue(currentValue, nextValue)) {
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
    insightsPanelAgentOptimisticallySet(
      state,
      action: PayloadAction<{ agentId: string; expanded: boolean }>,
    ) {
      const target = state.insightsPanelExpandedByAgent
      target.value = {
        ...target.value,
        [action.payload.agentId]: action.payload.expanded,
      }
    },
    insightsPanelAgentPersisted(
      state,
      action: PayloadAction<{ agentId: string; attemptedValue: boolean }>,
    ) {
      const target = state.insightsPanelExpandedByAgent
      target.persistedValue = {
        ...target.persistedValue,
        [action.payload.agentId]: action.payload.attemptedValue,
      }
    },
    insightsPanelAgentRolledBack(
      state,
      action: PayloadAction<{ agentId: string; attemptedValue: boolean }>,
    ) {
      const target = state.insightsPanelExpandedByAgent
      if (target.value[action.payload.agentId] !== action.payload.attemptedValue) {
        return
      }
      const nextValue = { ...target.value }
      if (Object.prototype.hasOwnProperty.call(target.persistedValue, action.payload.agentId)) {
        nextValue[action.payload.agentId] = target.persistedValue[action.payload.agentId]
      } else {
        delete nextValue[action.payload.agentId]
      }
      target.value = nextValue
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

export function persistInsightsPanelExpandedPreference(agentId: string, expanded: boolean) {
  return async (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
    const normalizedAgentId = agentId.trim()
    if (!normalizedAgentId) {
      return
    }

    const field = 'insightsPanelExpandedByAgent' as const
    if (!getState().agentRosterPreferences.insightsPanelExpandedByAgent.hydrated) {
      return
    }

    const writeToken = Symbol(normalizedAgentId)
    latestInsightsPanelWriteByAgent.set(normalizedAgentId, writeToken)
    dispatch(agentRosterPreferencesActions.insightsPanelAgentOptimisticallySet({
      agentId: normalizedAgentId,
      expanded,
    }))
    updateRosterPreferenceInCache(
      extra?.queryClient,
      field,
      getState().agentRosterPreferences.insightsPanelExpandedByAgent.value,
    )

    const previousWrite = insightsPanelWriteQueueByAgent.get(normalizedAgentId) ?? Promise.resolve()
    const currentWrite = previousWrite
      .catch(() => undefined)
      .then(async () => {
        try {
          await updateUserPreferences({
            preferences: {
              [USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT]: {
                [normalizedAgentId]: expanded,
              },
            },
          })
          dispatch(agentRosterPreferencesActions.insightsPanelAgentPersisted({
            agentId: normalizedAgentId,
            attemptedValue: expanded,
          }))
        } catch {
          if (latestInsightsPanelWriteByAgent.get(normalizedAgentId) === writeToken) {
            dispatch(agentRosterPreferencesActions.insightsPanelAgentRolledBack({
              agentId: normalizedAgentId,
              attemptedValue: expanded,
            }))
          }
        }

        updateRosterPreferenceInCache(
          extra?.queryClient,
          field,
          getState().agentRosterPreferences.insightsPanelExpandedByAgent.value,
        )
      })
    insightsPanelWriteQueueByAgent.set(normalizedAgentId, currentWrite)

    try {
      await currentWrite
    } finally {
      if (insightsPanelWriteQueueByAgent.get(normalizedAgentId) === currentWrite) {
        insightsPanelWriteQueueByAgent.delete(normalizedAgentId)
      }
      if (latestInsightsPanelWriteByAgent.get(normalizedAgentId) === writeToken) {
        latestInsightsPanelWriteByAgent.delete(normalizedAgentId)
      }
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
export const selectInsightsPanelExpandedPreference = (
  state: RootState,
  agentId: string | null,
): boolean | null => {
  const values = state.agentRosterPreferences.insightsPanelExpandedByAgent.value
  if (!agentId || !Object.prototype.hasOwnProperty.call(values, agentId)) {
    return null
  }
  const value = values[agentId]
  return typeof value === 'boolean' ? value : null
}
export const selectInsightsPanelPreferenceHydrated = (state: RootState): boolean => (
  state.agentRosterPreferences.insightsPanelExpandedByAgent.hydrated
)
export const selectSuggestionsEnabled = (state: RootState): boolean => state.agentRosterPreferences.suggestionsEnabled.value
export const selectSuggestionsPreferenceHydrated = (state: RootState): boolean => state.agentRosterPreferences.suggestionsEnabled.hydrated
export const selectAgentChatNotificationsEnabled = (state: RootState): boolean => state.agentRosterPreferences.agentChatNotificationsEnabled.value

import type { QueryClient } from '@tanstack/react-query'
import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import {
  parseBooleanPreference,
  parseFavoriteAgentIdsPreference,
  parseNullableBooleanPreference,
  updateUserPreferences,
  USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED,
  USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
} from '../api/userPreferences'
import type { AgentRosterSortMode } from '../types/agentRoster'
import { parseAgentRosterSortMode } from '../util/agentRosterSort'
import type { AppDispatch, RootState } from './appStore'

type PreferenceStatus = 'idle' | 'saving' | 'error'

type PreferenceFieldState<T> = {
  value: T
  persistedValue: T
  hydrated: boolean
  status: PreferenceStatus
  errorMessage: string | null
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

const AGENT_ROSTER_QUERY_KEY = ['agent-roster'] as const

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
    status: 'idle',
    errorMessage: null,
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
  switch (field) {
    case 'sortMode':
      return parseAgentRosterSortMode(value) as AgentRosterPreferencesState[K]['value']
    case 'favoriteAgentIds':
    case 'mutedAgentIds':
      return parseFavoriteAgentIdsPreference(value) as AgentRosterPreferencesState[K]['value']
    case 'insightsPanelExpanded':
      return parseNullableBooleanPreference(value) as AgentRosterPreferencesState[K]['value']
    case 'agentChatNotificationsEnabled':
      return parseBooleanPreference(value) as AgentRosterPreferencesState[K]['value']
  }
}

function preferenceKeyForField(field: AgentRosterPreferenceField): string {
  switch (field) {
    case 'sortMode':
      return USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE
    case 'favoriteAgentIds':
      return USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS
    case 'mutedAgentIds':
      return USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS
    case 'insightsPanelExpanded':
      return USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED
    case 'agentChatNotificationsEnabled':
      return USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED
  }
}

function rosterQueryFieldForPreference(field: AgentRosterPreferenceField): keyof AgentRosterQueryData {
  switch (field) {
    case 'sortMode':
      return 'agentRosterSortMode'
    default:
      return field
  }
}

function updateRosterPreferenceInCache<K extends AgentRosterPreferenceField>(
  queryClient: QueryClient | null | undefined,
  field: K,
  nextValue: AgentRosterPreferencesState[K]['value'],
): void {
  if (!queryClient) {
    return
  }
  const queryField = rosterQueryFieldForPreference(field)
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
  options: { persisted?: boolean; status?: PreferenceStatus; errorMessage?: string | null } = {},
): void {
  const target = state[field] as PreferenceFieldState<AgentRosterPreferencesState[K]['value']>
  const persistedValue = options.persisted ? value : target.persistedValue
  const nextStatus = options.status ?? target.status
  const nextErrorMessage = Object.prototype.hasOwnProperty.call(options, 'errorMessage')
    ? options.errorMessage ?? null
    : target.errorMessage
  if (
    target.hydrated
    && samePreferenceValue(target.value, value)
    && samePreferenceValue(target.persistedValue, persistedValue)
    && target.status === nextStatus
    && target.errorMessage === nextErrorMessage
  ) {
    return
  }
  target.value = value
  target.hydrated = true
  if (options.persisted) {
    target.persistedValue = value
  }
  if (options.status) {
    target.status = options.status
  }
  if (Object.prototype.hasOwnProperty.call(options, 'errorMessage')) {
    target.errorMessage = options.errorMessage ?? null
  }
}

const agentRosterPreferencesSlice = createSlice({
  name: 'agentRosterPreferences',
  initialState,
  reducers: {
    hydratedFromRoster(state, action: PayloadAction<AgentRosterPreferencesHydrationPayload>) {
      const payload = action.payload
      if (payload.sortMode !== undefined && !state.sortMode.hydrated) {
        assignPreferenceValue(state, 'sortMode', normalizeFieldValue('sortMode', payload.sortMode), { persisted: true })
      }
      if (payload.favoriteAgentIds !== undefined) {
        assignPreferenceValue(state, 'favoriteAgentIds', normalizeFieldValue('favoriteAgentIds', payload.favoriteAgentIds), { persisted: true })
      }
      if (payload.mutedAgentIds !== undefined) {
        assignPreferenceValue(state, 'mutedAgentIds', normalizeFieldValue('mutedAgentIds', payload.mutedAgentIds), { persisted: true })
      }
      if (payload.insightsPanelExpanded !== undefined && !state.insightsPanelExpanded.hydrated) {
        assignPreferenceValue(state, 'insightsPanelExpanded', normalizeFieldValue('insightsPanelExpanded', payload.insightsPanelExpanded), { persisted: true })
      }
      if (payload.agentChatNotificationsEnabled !== undefined && !state.agentChatNotificationsEnabled.hydrated) {
        assignPreferenceValue(state, 'agentChatNotificationsEnabled', normalizeFieldValue('agentChatNotificationsEnabled', payload.agentChatNotificationsEnabled), { persisted: true })
      }
    },
    preferenceOptimisticallySet<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K; value: AgentRosterPreferencesState[K]['value'] }>,
    ) {
      assignPreferenceValue(state, action.payload.field, action.payload.value, {
        status: 'saving',
        errorMessage: null,
      })
    },
    preferencePersisted<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K; value: AgentRosterPreferencesState[K]['value'] }>,
    ) {
      assignPreferenceValue(state, action.payload.field, action.payload.value, {
        persisted: true,
        status: 'idle',
        errorMessage: null,
      })
    },
    preferenceRolledBack<K extends AgentRosterPreferenceField>(
      state: AgentRosterPreferencesState,
      action: PayloadAction<{ field: K; message: string }>,
    ) {
      const target = state[action.payload.field] as PreferenceFieldState<AgentRosterPreferencesState[K]['value']>
      target.value = target.persistedValue
      target.status = 'error'
      target.errorMessage = action.payload.message
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
    const preferenceKey = preferenceKeyForField(field)
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
        message: 'Unable to save preference.',
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

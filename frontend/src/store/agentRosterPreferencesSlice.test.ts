import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { updateUserPreferences } from '../api/userPreferences'
import { createAppStore } from './appStore'
import {
  agentRosterPreferencesActions,
  persistAgentRosterPreference,
  selectAgentRosterPreferencesState,
  toggleAgentRosterStringPreference,
} from './agentRosterPreferencesSlice'

vi.mock('../api/userPreferences', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/userPreferences')>()
  return {
    ...actual,
    updateUserPreferences: vi.fn(),
  }
})

describe('agentRosterPreferencesSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hydrates preferences from roster query data', () => {
    const store = createAppStore()

    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({
      sortMode: 'alphabetical',
      favoriteAgentIds: ['agent-1'],
      mutedAgentIds: ['agent-2'],
      insightsPanelExpanded: true,
      suggestionsEnabled: false,
      agentChatNotificationsEnabled: true,
    }))

    expect(selectAgentRosterPreferencesState(store.getState())).toMatchObject({
      sortMode: { value: 'alphabetical', persistedValue: 'alphabetical', hydrated: true },
      favoriteAgentIds: { value: ['agent-1'], persistedValue: ['agent-1'], hydrated: true },
      mutedAgentIds: { value: ['agent-2'], persistedValue: ['agent-2'], hydrated: true },
      insightsPanelExpanded: { value: true, persistedValue: true, hydrated: true },
      suggestionsEnabled: { value: false, persistedValue: false, hydrated: true },
      agentChatNotificationsEnabled: { value: true, persistedValue: true, hydrated: true },
    })
  })

  it('optimistically persists and normalizes roster cache values', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      favoriteAgentIds: [],
    })
    vi.mocked(updateUserPreferences).mockResolvedValue({
      preferences: {
        'agent.chat.roster.favorite_agent_ids': ['agent-1'],
      },
    })
    const store = createAppStore({ queryClient })

    await store.dispatch(toggleAgentRosterStringPreference('favoriteAgentIds', 'agent-1'))

    expect(selectAgentRosterPreferencesState(store.getState()).favoriteAgentIds).toMatchObject({
      value: ['agent-1'],
      persistedValue: ['agent-1'],
    })
    expect(queryClient.getQueryData<{ favoriteAgentIds: string[] }>(['agent-roster'])?.favoriteAgentIds).toEqual(['agent-1'])
  })

  it('optimistically disables suggestions and updates the roster cache', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      suggestionsEnabled: true,
    })
    vi.mocked(updateUserPreferences).mockResolvedValue({
      preferences: {
        'agent.chat.suggestions.enabled': false,
      },
    })
    const store = createAppStore({ queryClient })
    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({ suggestionsEnabled: true }))

    await store.dispatch(persistAgentRosterPreference('suggestionsEnabled', false))

    expect(updateUserPreferences).toHaveBeenCalledWith({
      preferences: {
        'agent.chat.suggestions.enabled': false,
      },
    })
    expect(selectAgentRosterPreferencesState(store.getState()).suggestionsEnabled).toMatchObject({
      value: false,
      persistedValue: false,
    })
    expect(queryClient.getQueryData<{ suggestionsEnabled: boolean }>(['agent-roster'])?.suggestionsEnabled).toBe(false)
  })

  it('rolls back Redux and roster cache when persistence fails', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      agentRosterSortMode: 'recent',
    })
    vi.mocked(updateUserPreferences).mockRejectedValue(new Error('nope'))
    const store = createAppStore({ queryClient })
    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({ sortMode: 'recent' }))

    await store.dispatch(persistAgentRosterPreference('sortMode', 'alphabetical'))

    expect(selectAgentRosterPreferencesState(store.getState()).sortMode).toMatchObject({
      value: 'recent',
      persistedValue: 'recent',
    })
    expect(queryClient.getQueryData<{ agentRosterSortMode: string }>(['agent-roster'])?.agentRosterSortMode).toBe('recent')
  })
})

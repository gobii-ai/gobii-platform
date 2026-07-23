import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { updateUserPreferences } from '../api/userPreferences'
import { createAppStore } from './appStore'
import {
  agentRosterPreferencesActions,
  persistAgentRosterPreference,
  persistInsightsPanelExpandedPreference,
  selectAgentRosterPreferencesState,
  selectInsightsPanelExpandedPreference,
  toggleAgentRosterStringPreference,
} from './agentRosterPreferencesSlice'

vi.mock('../api/userPreferences', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/userPreferences')>()
  return {
    ...actual,
    updateUserPreferences: vi.fn(),
  }
})

const FIRST_AGENT_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_AGENT_ID = '22222222-2222-4222-8222-222222222222'

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
      insightsPanelExpandedByAgent: {
        'agent-1': false,
        'agent-2': true,
      },
      suggestionsEnabled: false,
      agentChatNotificationsEnabled: true,
    }))

    expect(selectAgentRosterPreferencesState(store.getState())).toMatchObject({
      sortMode: { value: 'alphabetical', persistedValue: 'alphabetical', hydrated: true },
      favoriteAgentIds: { value: ['agent-1'], persistedValue: ['agent-1'], hydrated: true },
      mutedAgentIds: { value: ['agent-2'], persistedValue: ['agent-2'], hydrated: true },
      insightsPanelExpandedByAgent: {
        value: { 'agent-1': false, 'agent-2': true },
        persistedValue: { 'agent-1': false, 'agent-2': true },
        hydrated: true,
      },
      suggestionsEnabled: { value: false, persistedValue: false, hydrated: true },
      agentChatNotificationsEnabled: { value: true, persistedValue: true, hydrated: true },
    })
    expect(selectInsightsPanelExpandedPreference(store.getState(), 'agent-1')).toBe(false)
    expect(selectInsightsPanelExpandedPreference(store.getState(), 'agent-2')).toBe(true)
    expect(selectInsightsPanelExpandedPreference(store.getState(), 'agent-3')).toBeNull()
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

  it('does not write an agent entry before the whole preference map is hydrated', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      insightsPanelExpandedByAgent: {},
    })
    const store = createAppStore({ queryClient })

    await store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, false))

    expect(updateUserPreferences).not.toHaveBeenCalled()
    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent).toEqual({
      value: {},
      persistedValue: {},
      hydrated: false,
    })
    expect(queryClient.getQueryData<{ insightsPanelExpandedByAgent: Record<string, boolean> }>(
      ['agent-roster'],
    )?.insightsPanelExpandedByAgent).toEqual({})
  })

  it('serializes same-agent writes and rolls a failed newer write back to the last success', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      insightsPanelExpandedByAgent: { [SECOND_AGENT_ID]: false },
    })
    let resolveFirst: ((value: { preferences: Record<string, unknown> }) => void) | undefined
    let rejectSecond: ((reason?: unknown) => void) | undefined
    vi.mocked(updateUserPreferences)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSecond = reject }))
    const store = createAppStore({ queryClient })
    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({
      insightsPanelExpandedByAgent: { [SECOND_AGENT_ID]: false },
    }))

    const firstRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, false))
    const secondRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, true))

    await vi.waitFor(() => expect(updateUserPreferences).toHaveBeenCalledTimes(1))
    expect(updateUserPreferences).toHaveBeenCalledWith({
      preferences: {
        'agent.chat.insights_panel.expanded_by_agent': { [FIRST_AGENT_ID]: false },
      },
    })
    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent.value).toEqual({
      [FIRST_AGENT_ID]: true,
      [SECOND_AGENT_ID]: false,
    })

    resolveFirst?.({
      preferences: {
        'agent.chat.insights_panel.expanded_by_agent': {
          [FIRST_AGENT_ID]: false,
          [SECOND_AGENT_ID]: false,
        },
      },
    })
    await firstRequest

    await vi.waitFor(() => expect(updateUserPreferences).toHaveBeenCalledTimes(2))
    expect(updateUserPreferences).toHaveBeenNthCalledWith(2, {
      preferences: {
        'agent.chat.insights_panel.expanded_by_agent': { [FIRST_AGENT_ID]: true },
      },
    })
    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent).toMatchObject({
      value: { [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: false },
      persistedValue: { [FIRST_AGENT_ID]: false, [SECOND_AGENT_ID]: false },
    })

    rejectSecond?.(new Error('nope'))
    await secondRequest

    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent).toMatchObject({
      value: { [FIRST_AGENT_ID]: false, [SECOND_AGENT_ID]: false },
      persistedValue: { [FIRST_AGENT_ID]: false, [SECOND_AGENT_ID]: false },
    })
    expect(queryClient.getQueryData<{ insightsPanelExpandedByAgent: Record<string, boolean> }>(
      ['agent-roster'],
    )?.insightsPanelExpandedByAgent).toEqual({
      [FIRST_AGENT_ID]: false,
      [SECOND_AGENT_ID]: false,
    })
  })

  it('does not let an older repeated-value failure roll back the latest write', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      insightsPanelExpandedByAgent: { [FIRST_AGENT_ID]: false },
    })
    let rejectFirst: ((reason?: unknown) => void) | undefined
    vi.mocked(updateUserPreferences)
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockResolvedValueOnce({
        preferences: {
          'agent.chat.insights_panel.expanded_by_agent': { [FIRST_AGENT_ID]: false },
        },
      })
      .mockResolvedValueOnce({
        preferences: {
          'agent.chat.insights_panel.expanded_by_agent': { [FIRST_AGENT_ID]: true },
        },
      })
    const store = createAppStore({ queryClient })
    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({
      insightsPanelExpandedByAgent: { [FIRST_AGENT_ID]: false },
    }))

    const firstRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, true))
    const secondRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, false))
    const thirdRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, true))

    await vi.waitFor(() => expect(updateUserPreferences).toHaveBeenCalledTimes(1))
    rejectFirst?.(new Error('nope'))
    await Promise.all([firstRequest, secondRequest, thirdRequest])

    expect(updateUserPreferences).toHaveBeenCalledTimes(3)
    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent).toMatchObject({
      value: { [FIRST_AGENT_ID]: true },
      persistedValue: { [FIRST_AGENT_ID]: true },
    })
    expect(queryClient.getQueryData<{ insightsPanelExpandedByAgent: Record<string, boolean> }>(
      ['agent-roster'],
    )?.insightsPanelExpandedByAgent).toEqual({ [FIRST_AGENT_ID]: true })
  })

  it('rolls back only the failed agent while another agent update remains optimistic', async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(['agent-roster'], {
      agents: [],
      insightsPanelExpandedByAgent: { [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: false },
    })
    let rejectFirst: ((reason?: unknown) => void) | undefined
    let resolveSecond: ((value: { preferences: Record<string, unknown> }) => void) | undefined
    vi.mocked(updateUserPreferences)
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
    const store = createAppStore({ queryClient })
    store.dispatch(agentRosterPreferencesActions.hydratedFromRoster({
      insightsPanelExpandedByAgent: { [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: false },
    }))

    const failedRequest = store.dispatch(persistInsightsPanelExpandedPreference(FIRST_AGENT_ID, false))
    const pendingRequest = store.dispatch(persistInsightsPanelExpandedPreference(SECOND_AGENT_ID, true))
    await vi.waitFor(() => expect(updateUserPreferences).toHaveBeenCalledTimes(2))
    rejectFirst?.(new Error('nope'))
    await failedRequest

    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent).toMatchObject({
      value: { [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: true },
      persistedValue: { [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: false },
    })
    expect(queryClient.getQueryData<{ insightsPanelExpandedByAgent: Record<string, boolean> }>(
      ['agent-roster'],
    )?.insightsPanelExpandedByAgent).toEqual({ [FIRST_AGENT_ID]: true, [SECOND_AGENT_ID]: true })

    resolveSecond?.({
      preferences: {
        'agent.chat.insights_panel.expanded_by_agent': {
          [FIRST_AGENT_ID]: true,
          [SECOND_AGENT_ID]: true,
        },
      },
    })
    await pendingRequest
    expect(selectAgentRosterPreferencesState(store.getState()).insightsPanelExpandedByAgent.persistedValue).toEqual({
      [FIRST_AGENT_ID]: true,
      [SECOND_AGENT_ID]: true,
    })
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

import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { RootState } from './appStore'

export type AgentResourceStatus = 'idle' | 'loading' | 'success' | 'error'

type AgentScopedResourceStatus = {
  status: AgentResourceStatus
  errorMessage: string | null
  updating: boolean
}

type UsageSummaryStatus = {
  status: AgentResourceStatus
  errorMessage: string | null
}

export type AgentResourceStatusState = {
  quickSettingsByAgentId: Record<string, AgentScopedResourceStatus>
  addonsByAgentId: Record<string, AgentScopedResourceStatus>
  agentChatUsageSummary: UsageSummaryStatus
}

const initialScopedStatus: AgentScopedResourceStatus = {
  status: 'idle',
  errorMessage: null,
  updating: false,
}

const initialState: AgentResourceStatusState = {
  quickSettingsByAgentId: {},
  addonsByAgentId: {},
  agentChatUsageSummary: {
    status: 'idle',
    errorMessage: null,
  },
}

function ensureAgentStatus(
  records: Record<string, AgentScopedResourceStatus>,
  agentId: string,
): AgentScopedResourceStatus {
  if (!records[agentId]) {
    records[agentId] = { ...initialScopedStatus }
  }
  return records[agentId]
}

const agentResourceStatusSlice = createSlice({
  name: 'agentResourceStatus',
  initialState,
  reducers: {
    quickSettingsLoading(state, action: PayloadAction<string>) {
      const status = ensureAgentStatus(state.quickSettingsByAgentId, action.payload)
      status.status = 'loading'
      status.errorMessage = null
    },
    quickSettingsLoaded(state, action: PayloadAction<string>) {
      const status = ensureAgentStatus(state.quickSettingsByAgentId, action.payload)
      status.status = 'success'
      status.errorMessage = null
    },
    quickSettingsFailed(state, action: PayloadAction<{ agentId: string; message: string }>) {
      const status = ensureAgentStatus(state.quickSettingsByAgentId, action.payload.agentId)
      status.status = 'error'
      status.errorMessage = action.payload.message
    },
    quickSettingsUpdatingSet(state, action: PayloadAction<{ agentId: string; updating: boolean }>) {
      ensureAgentStatus(state.quickSettingsByAgentId, action.payload.agentId).updating = action.payload.updating
    },
    addonsLoading(state, action: PayloadAction<string>) {
      const status = ensureAgentStatus(state.addonsByAgentId, action.payload)
      status.status = 'loading'
      status.errorMessage = null
    },
    addonsLoaded(state, action: PayloadAction<string>) {
      const status = ensureAgentStatus(state.addonsByAgentId, action.payload)
      status.status = 'success'
      status.errorMessage = null
    },
    addonsFailed(state, action: PayloadAction<{ agentId: string; message: string }>) {
      const status = ensureAgentStatus(state.addonsByAgentId, action.payload.agentId)
      status.status = 'error'
      status.errorMessage = action.payload.message
    },
    addonsUpdatingSet(state, action: PayloadAction<{ agentId: string; updating: boolean }>) {
      ensureAgentStatus(state.addonsByAgentId, action.payload.agentId).updating = action.payload.updating
    },
    agentChatUsageSummaryLoading(state) {
      state.agentChatUsageSummary.status = 'loading'
      state.agentChatUsageSummary.errorMessage = null
    },
    agentChatUsageSummaryLoaded(state) {
      state.agentChatUsageSummary.status = 'success'
      state.agentChatUsageSummary.errorMessage = null
    },
    agentChatUsageSummaryFailed(state, action: PayloadAction<string>) {
      state.agentChatUsageSummary.status = 'error'
      state.agentChatUsageSummary.errorMessage = action.payload
    },
  },
})

export const agentResourceStatusActions = agentResourceStatusSlice.actions
export const agentResourceStatusReducer = agentResourceStatusSlice.reducer

export const selectAgentResourceStatusState = (state: RootState): AgentResourceStatusState => state.agentResourceStatus
export const selectQuickSettingsResourceStatus = (agentId: string | null | undefined) => (state: RootState) => (
  agentId ? state.agentResourceStatus.quickSettingsByAgentId[agentId] ?? null : null
)
export const selectAddonsResourceStatus = (agentId: string | null | undefined) => (state: RootState) => (
  agentId ? state.agentResourceStatus.addonsByAgentId[agentId] ?? null : null
)
export const selectAgentChatUsageSummaryStatus = (state: RootState) => state.agentResourceStatus.agentChatUsageSummary

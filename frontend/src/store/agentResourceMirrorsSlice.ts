import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { AgentAddonsResponse } from '../types/agentAddons'
import type { AgentQuickSettingsResponse } from '../types/agentQuickSettings'
import type { UsageSummaryResponse } from '../components/usage/types'
import type { RootState } from './appStore'

export type MirrorStatus = 'idle' | 'loading' | 'success' | 'error'

export type AgentResourceMirrorsState = {
  quickSettingsByAgentId: Record<string, {
    data: AgentQuickSettingsResponse | null
    status: MirrorStatus
    errorMessage: string | null
    updating: boolean
  }>
  addonsByAgentId: Record<string, {
    data: AgentAddonsResponse | null
    status: MirrorStatus
    errorMessage: string | null
    updating: boolean
  }>
  agentChatUsageSummary: {
    data: UsageSummaryResponse | null
    status: MirrorStatus
    errorMessage: string | null
  }
}

const initialState: AgentResourceMirrorsState = {
  quickSettingsByAgentId: {},
  addonsByAgentId: {},
  agentChatUsageSummary: {
    data: null,
    status: 'idle',
    errorMessage: null,
  },
}

function ensureAgentMirror<T extends { data: unknown; status: MirrorStatus; errorMessage: string | null; updating: boolean }>(
  records: Record<string, T>,
  agentId: string,
  initialData: T['data'],
): T {
  if (!records[agentId]) {
    records[agentId] = {
      data: initialData,
      status: 'idle',
      errorMessage: null,
      updating: false,
    } as T
  }
  return records[agentId]
}

const agentResourceMirrorsSlice = createSlice({
  name: 'agentResourceMirrors',
  initialState,
  reducers: {
    quickSettingsLoading(state, action: PayloadAction<string>) {
      const mirror = ensureAgentMirror(state.quickSettingsByAgentId, action.payload, null)
      mirror.status = 'loading'
      mirror.errorMessage = null
    },
    quickSettingsLoaded(state, action: PayloadAction<{ agentId: string; data: AgentQuickSettingsResponse | null }>) {
      const mirror = ensureAgentMirror(state.quickSettingsByAgentId, action.payload.agentId, null)
      mirror.data = action.payload.data
      mirror.status = 'success'
      mirror.errorMessage = null
    },
    quickSettingsFailed(state, action: PayloadAction<{ agentId: string; message: string }>) {
      const mirror = ensureAgentMirror(state.quickSettingsByAgentId, action.payload.agentId, null)
      mirror.status = 'error'
      mirror.errorMessage = action.payload.message
    },
    quickSettingsUpdatingSet(state, action: PayloadAction<{ agentId: string; updating: boolean }>) {
      ensureAgentMirror(state.quickSettingsByAgentId, action.payload.agentId, null).updating = action.payload.updating
    },
    addonsLoading(state, action: PayloadAction<string>) {
      const mirror = ensureAgentMirror(state.addonsByAgentId, action.payload, null)
      mirror.status = 'loading'
      mirror.errorMessage = null
    },
    addonsLoaded(state, action: PayloadAction<{ agentId: string; data: AgentAddonsResponse | null }>) {
      const mirror = ensureAgentMirror(state.addonsByAgentId, action.payload.agentId, null)
      mirror.data = action.payload.data
      mirror.status = 'success'
      mirror.errorMessage = null
    },
    addonsFailed(state, action: PayloadAction<{ agentId: string; message: string }>) {
      const mirror = ensureAgentMirror(state.addonsByAgentId, action.payload.agentId, null)
      mirror.status = 'error'
      mirror.errorMessage = action.payload.message
    },
    addonsUpdatingSet(state, action: PayloadAction<{ agentId: string; updating: boolean }>) {
      ensureAgentMirror(state.addonsByAgentId, action.payload.agentId, null).updating = action.payload.updating
    },
    agentChatUsageSummaryLoading(state) {
      state.agentChatUsageSummary.status = 'loading'
      state.agentChatUsageSummary.errorMessage = null
    },
    agentChatUsageSummaryLoaded(state, action: PayloadAction<UsageSummaryResponse | null>) {
      state.agentChatUsageSummary.data = action.payload
      state.agentChatUsageSummary.status = 'success'
      state.agentChatUsageSummary.errorMessage = null
    },
    agentChatUsageSummaryFailed(state, action: PayloadAction<string>) {
      state.agentChatUsageSummary.status = 'error'
      state.agentChatUsageSummary.errorMessage = action.payload
    },
  },
})

export const agentResourceMirrorsActions = agentResourceMirrorsSlice.actions
export const agentResourceMirrorsReducer = agentResourceMirrorsSlice.reducer

export const selectAgentResourceMirrorsState = (state: RootState): AgentResourceMirrorsState => state.agentResourceMirrors
export const selectQuickSettingsMirror = (agentId: string | null | undefined) => (state: RootState) => (
  agentId ? state.agentResourceMirrors.quickSettingsByAgentId[agentId] ?? null : null
)
export const selectAddonsMirror = (agentId: string | null | undefined) => (state: RootState) => (
  agentId ? state.agentResourceMirrors.addonsByAgentId[agentId] ?? null : null
)
export const selectAgentChatUsageSummaryMirror = (state: RootState) => state.agentResourceMirrors.agentChatUsageSummary

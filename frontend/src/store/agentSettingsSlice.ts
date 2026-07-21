import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import { updateAgent } from '../api/agents'
import { refreshProcessing } from './chatSlice'
import type { AppDispatch, RootState } from './appStore'

export type AgentSettingsState = {
  draftTier: string
  tierOverridesByAgentId: Record<string, string>
  savingByAgentId: Record<string, boolean>
  errorByAgentId: Record<string, string | null>
}

const initialState: AgentSettingsState = {
  draftTier: 'standard',
  tierOverridesByAgentId: {},
  savingByAgentId: {},
  errorByAgentId: {},
}

const agentSettingsSlice = createSlice({
  name: 'agentSettings',
  initialState,
  reducers: {
    draftTierSet(state, action: PayloadAction<string>) {
      state.draftTier = action.payload
    },
    draftTierReset(state) {
      state.draftTier = 'standard'
    },
    tierOverrideSet(state, action: PayloadAction<{ agentId: string; tier: string }>) {
      state.tierOverridesByAgentId[action.payload.agentId] = action.payload.tier
    },
    tierOverrideCleared(state, action: PayloadAction<string>) {
      delete state.tierOverridesByAgentId[action.payload]
    },
    tierSavingSet(state, action: PayloadAction<{ agentId: string; saving: boolean }>) {
      state.savingByAgentId[action.payload.agentId] = action.payload.saving
    },
    tierErrorSet(state, action: PayloadAction<{ agentId: string; message: string | null }>) {
      state.errorByAgentId[action.payload.agentId] = action.payload.message
    },
    workflowResetForAgent(state, action: PayloadAction<string | null>) {
      if (!action.payload) {
        state.draftTier = 'standard'
        return
      }
      state.savingByAgentId[action.payload] = false
      state.errorByAgentId[action.payload] = null
    },
  },
})

export const agentSettingsActions = agentSettingsSlice.actions
export const agentSettingsReducer = agentSettingsSlice.reducer

export function updateAgentIntelligenceTier({
  agentId,
  tier,
  previousTier,
  refetchQuickSettings,
}: {
  agentId: string
  tier: string
  previousTier: string
  refetchQuickSettings?: () => void | Promise<unknown>
}) {
  return async (dispatch: AppDispatch, _getState: () => RootState, extra?: { queryClient?: { invalidateQueries: (filters: { queryKey: unknown[]; exact?: boolean }) => Promise<unknown> | void } | null }) => {
    dispatch(agentSettingsActions.tierOverrideSet({ agentId, tier }))
    dispatch(agentSettingsActions.tierSavingSet({ agentId, saving: true }))
    dispatch(agentSettingsActions.tierErrorSet({ agentId, message: null }))
    try {
      await updateAgent(agentId, { preferred_llm_tier: tier })
      void extra?.queryClient?.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      void extra?.queryClient?.invalidateQueries({ queryKey: ['agent-quick-settings', agentId], exact: true })
      void refetchQuickSettings?.()
      void dispatch(refreshProcessing({ agentId }))
      return true
    } catch {
      dispatch(agentSettingsActions.tierOverrideSet({ agentId, tier: previousTier }))
      dispatch(agentSettingsActions.tierErrorSet({ agentId, message: 'Unable to update intelligence level.' }))
      return false
    } finally {
      dispatch(agentSettingsActions.tierSavingSet({ agentId, saving: false }))
    }
  }
}

export const selectAgentSettingsState = (state: RootState): AgentSettingsState => state.agentSettings
export const selectDraftIntelligenceTier = (state: RootState): string => state.agentSettings.draftTier
export const selectAgentTierOverrides = (state: RootState): Record<string, string> => state.agentSettings.tierOverridesByAgentId
export const selectAgentTierSavingById = (state: RootState): Record<string, boolean> => state.agentSettings.savingByAgentId
export const selectAgentTierErrorById = (state: RootState): Record<string, string | null> => state.agentSettings.errorByAgentId

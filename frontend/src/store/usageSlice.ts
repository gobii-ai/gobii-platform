import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { UsageAgent, UsageSummaryResponse } from '../components/usage/types'
import type { RootState } from './appStore'

export type UsageStatus = 'idle' | 'loading' | 'success' | 'error'

export type UsageState = {
  summary: UsageSummaryResponse | null
  summaryStatus: UsageStatus
  summaryErrorMessage: string | null
  agents: UsageAgent[]
  agentsStatus: UsageStatus
  agentsErrorMessage: string | null
}

export const initialUsageState: UsageState = {
  summary: null,
  summaryStatus: 'idle',
  summaryErrorMessage: null,
  agents: [],
  agentsStatus: 'idle',
  agentsErrorMessage: null,
}

const usageSlice = createSlice({
  name: 'usage',
  initialState: initialUsageState,
  reducers: {
    summaryLoading(state) {
      state.summaryStatus = 'loading'
      state.summaryErrorMessage = null
    },
    summaryLoaded(state, action: PayloadAction<UsageSummaryResponse>) {
      state.summary = action.payload
      state.summaryStatus = 'success'
      state.summaryErrorMessage = null
    },
    summaryFailed(state, action: PayloadAction<string>) {
      state.summaryStatus = 'error'
      state.summaryErrorMessage = action.payload
    },
    agentsLoading(state) {
      state.agentsStatus = 'loading'
      state.agentsErrorMessage = null
    },
    agentsLoaded(state, action: PayloadAction<UsageAgent[]>) {
      state.agents = action.payload
      state.agentsStatus = 'success'
      state.agentsErrorMessage = null
    },
    agentsFailed(state, action: PayloadAction<string>) {
      state.agentsStatus = 'error'
      state.agentsErrorMessage = action.payload
    },
    resetUsageState() {
      return initialUsageState
    },
  },
})

export const usageActions = usageSlice.actions
export const usageReducer = usageSlice.reducer

export const selectUsageState = (state: RootState): UsageState => state.usage

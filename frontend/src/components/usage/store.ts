import { create } from 'zustand'

import type { UsageAgent, UsageSummaryResponse } from './types'

type UsageStatus = 'idle' | 'loading' | 'success' | 'error'

type UsageState = {
  summary: UsageSummaryResponse | null
  summaryStatus: UsageStatus
  summaryErrorMessage: string | null
  agents: UsageAgent[]
  agentsStatus: UsageStatus
  agentsErrorMessage: string | null
  setSummaryLoading: () => void
  setSummaryData: (summary: UsageSummaryResponse) => void
  setSummaryError: (message: string) => void
  setAgentsLoading: () => void
  setAgentsData: (agents: UsageAgent[]) => void
  setAgentsError: (message: string) => void
  reset: () => void
}

export const useUsageStore = create<UsageState>((set) => ({
  summary: null,
  summaryStatus: 'idle',
  summaryErrorMessage: null,
  agents: [],
  agentsStatus: 'idle',
  agentsErrorMessage: null,
  setSummaryLoading: () => set({ summaryStatus: 'loading', summaryErrorMessage: null }),
  setSummaryData: (summary) => set({
    summary,
    summaryStatus: 'success',
    summaryErrorMessage: null,
  }),
  setSummaryError: (message) => set({
    summaryStatus: 'error',
    summaryErrorMessage: message,
  }),
  setAgentsLoading: () => set({ agentsStatus: 'loading', agentsErrorMessage: null }),
  setAgentsData: (agents) => set({
    agents,
    agentsStatus: 'success',
    agentsErrorMessage: null,
  }),
  setAgentsError: (message) => set({
    agentsStatus: 'error',
    agentsErrorMessage: message,
  }),
  reset: () => set({
    summary: null,
    summaryStatus: 'idle',
    summaryErrorMessage: null,
    agents: [],
    agentsStatus: 'idle',
    agentsErrorMessage: null,
  }),
}))

export type { UsageStatus }

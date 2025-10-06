import { create } from 'zustand'

import type { UsageSummaryResponse } from './types'

type UsageStatus = 'idle' | 'loading' | 'success' | 'error'

type UsageState = {
  summary: UsageSummaryResponse | null
  status: UsageStatus
  errorMessage: string | null
  setLoading: () => void
  setSummary: (summary: UsageSummaryResponse) => void
  setError: (message: string) => void
  reset: () => void
}

export const useUsageStore = create<UsageState>((set) => ({
  summary: null,
  status: 'idle',
  errorMessage: null,
  setLoading: () => set({ status: 'loading', errorMessage: null }),
  setSummary: (summary) => set({
    summary,
    status: 'success',
    errorMessage: null,
  }),
  setError: (message) => set((state) => ({
    status: 'error',
    errorMessage: message,
    summary: state.summary,
  })),
  reset: () => set({ summary: null, status: 'idle', errorMessage: null }),
}))

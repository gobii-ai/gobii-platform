import type { UsageSummaryQueryInput, UsageSummaryResponse, UsageTrendQueryInput, UsageTrendResponse } from './types'

export const fetchUsageSummary = async (params: UsageSummaryQueryInput, signal: AbortSignal): Promise<UsageSummaryResponse> => {
  const search = new URLSearchParams()

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  const suffix = search.toString()
  const response = await fetch(`/console/api/usage/summary/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
    signal,
  })

  if (!response.ok) {
    throw new Error(`Usage summary request failed (${response.status})`)
  }

  return response.json()
}

export const fetchUsageTrends = async (params: UsageTrendQueryInput, signal: AbortSignal): Promise<UsageTrendResponse> => {
  const search = new URLSearchParams()
  search.set('mode', params.mode)

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  const response = await fetch(`/console/api/usage/trends/?${search.toString()}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
    signal,
  })

  if (!response.ok) {
    throw new Error(`Usage trends request failed (${response.status})`)
  }

  return response.json()
}
